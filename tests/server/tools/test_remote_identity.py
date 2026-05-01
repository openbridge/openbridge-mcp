from types import SimpleNamespace

import pytest
import requests as _requests
from pydantic import ValidationError

from src.server.tools import remote_identity


def test_get_remote_identities_paginates(monkeypatch):
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    responses = [
        SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": [{"id": "ri-1"}],
                "links": {"next": "https://remote-identity.api.openbridge.io/ri?page=2"},
            },
        ),
        SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": [{"id": "ri-2"}],
                "links": {"next": None},
            },
        ),
    ]

    def fake_get(url, headers=None, params=None, timeout=None):
        assert headers == {"Authorization": "token"}
        return responses.pop(0)

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    identities = remote_identity.get_remote_identities()

    assert identities == [{"id": "ri-1"}, {"id": "ri-2"}]


def test_get_remote_identities_uses_correct_type_param(monkeypatch):
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"data": [], "links": {"next": None}},
        )

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    remote_identity.get_remote_identities(remote_identity_type_id=14)

    assert captured["url"].endswith("/ri?page=1")
    assert captured["params"] == {"remote_identity_type": "14"}


def test_get_remote_identities_stops_on_failure(monkeypatch):
    """Non-auth failure (5xx) on first page preserves partial-results
    contract: returns []."""
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, params=None, timeout=None):
        return SimpleNamespace(status_code=500, json=lambda: {})

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    identities = remote_identity.get_remote_identities()

    assert identities == []


@pytest.mark.parametrize("status", [401, 403])
def test_get_remote_identities_raises_on_auth_failure(monkeypatch, status):
    """REGRESSION guard: auth failure on /ri must NOT masquerade as
    "no identities." 401/403 on the first call must raise ToolError so
    the MCP client sees the rejected-credential mode.
    """
    monkeypatch.setattr(
        remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "Bearer stale"}
    )

    def fake_get(url, headers=None, params=None, timeout=None):
        return SimpleNamespace(status_code=status, json=lambda: {})

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    result = remote_identity.get_remote_identities()
    assert result["error_kind"] == "auth_error"


def test_get_remote_identities_mid_pagination_401_returns_partial(monkeypatch):
    """Mid-pagination auth failure is unusual but must not raise — the
    JWT is unlikely to have just been revoked mid-call, and the caller
    has already consumed page 1. Preserve partial-results contract."""
    monkeypatch.setattr(
        remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"}
    )

    responses = [
        SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": [{"id": "ri-1"}],
                "links": {"next": "https://remote-identity.api.openbridge.io/ri?page=2"},
            },
        ),
        SimpleNamespace(status_code=401, json=lambda: {}),
    ]

    def fake_get(url, headers=None, params=None, timeout=None):
        return responses.pop(0)

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    # Must not raise; returns the rows collected on page 1.
    assert remote_identity.get_remote_identities() == [{"id": "ri-1"}]


def test_get_remote_identity_by_id_success(monkeypatch):
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, timeout=None):
        assert url.endswith("/ri/42")
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": {
                    "id": "42",
                    "attributes": {"region": "na", "status": "active"},
                    "relationships": {},
                }
            },
        )

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    identity = remote_identity.get_remote_identity_by_id(42)

    assert identity == {"id": "42", "relationships": {}, "region": "na", "status": "active"}


def test_get_remote_identity_by_id_rejects_string_id():
    with pytest.raises(ValidationError):
        remote_identity.get_remote_identity_by_id("42")


def test_get_remote_identity_by_id_not_found(monkeypatch):
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(status_code=404, json=lambda: {})

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    identity = remote_identity.get_remote_identity_by_id(404)
    assert identity["error_kind"] == "mcp_input_validation"


# ---------------------------------------------------------------------------
# Phase 2b — error path coverage for remote_identity
#
# Contract (docs/tool-contracts.md):
# - RequestException → never propagates, always returns a typed shape.
# - Malformed 200 responses (missing attributes, wrong types, empty data)
#   must never crash — always return the not-found shape or partial list.
# - Pagination is partial-results: a mid-stream failure returns rows
#   collected so far, not [].
# ---------------------------------------------------------------------------


def test_get_remote_identities_returns_partial_on_request_exception(monkeypatch):
    """REGRESSION guard for the partial-results contract: if page 2 of a
    paginated response raises, the rows from page 1 must still come back."""
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    responses = [
        SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": [{"id": "ri-1"}, {"id": "ri-2"}],
                "links": {"next": "https://remote-identity.api.openbridge.io/ri?page=2"},
            },
        ),
    ]

    def fake_get(url, headers=None, params=None, timeout=None):
        if responses:
            return responses.pop(0)
        raise _requests.ConnectionError("upstream died mid-pagination")

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    identities = remote_identity.get_remote_identities()

    # Contract: return the two rows we collected, don't raise or return [].
    assert identities == [{"id": "ri-1"}, {"id": "ri-2"}]


def test_get_remote_identities_network_failure_on_first_page(monkeypatch):
    """RequestException on the very first page returns [] without raising."""
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, params=None, timeout=None):
        raise _requests.Timeout("too slow")

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    result = remote_identity.get_remote_identities()
    assert result["error_kind"] == "sp_api_client"


def test_get_remote_identities_returns_partial_on_non_json(monkeypatch):
    """Non-JSON body mid-pagination is treated as end-of-stream (returns
    whatever was collected before the bad page)."""
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    responses = [
        SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": [{"id": "ri-1"}],
                "links": {"next": "https://remote-identity.api.openbridge.io/ri?page=2"},
            },
        ),
        SimpleNamespace(
            status_code=200,
            json=lambda: (_ for _ in ()).throw(ValueError("not json")),
        ),
    ]

    def fake_get(url, headers=None, params=None, timeout=None):
        return responses.pop(0)

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    assert remote_identity.get_remote_identities() == [{"id": "ri-1"}]


def test_get_remote_identity_by_id_network_failure_returns_error_shape(monkeypatch):
    """RequestException returns a structured error shape, never propagates."""
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, timeout=None):
        raise _requests.ConnectionError("host unreachable")

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    result = remote_identity.get_remote_identity_by_id(42)
    assert result["error_kind"] == "sp_api_client"


def test_get_remote_identity_by_id_empty_data_returns_not_found(monkeypatch):
    """REGRESSION: the user-reported malformed-payload crash. Empty `data`
    must not trigger `KeyError` on `data['attributes']`."""
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(status_code=200, json=lambda: {"data": {}})

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    result = remote_identity.get_remote_identity_by_id(42)
    assert result["error_kind"] == "mcp_input_validation"


def test_get_remote_identity_by_id_non_json_returns_not_found(monkeypatch):
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            json=lambda: (_ for _ in ()).throw(ValueError("not json")),
        )

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    result = remote_identity.get_remote_identity_by_id(42)
    assert result["error_kind"] == "mcp_input_validation"


# Representative (not exhaustive) malformed-payload shapes. Per the plan,
# cap at 3-5 per tool.
@pytest.mark.parametrize(
    "payload,scenario",
    [
        ({"data": None}, "data is None"),
        ({"data": {"id": "42"}}, "missing attributes key"),
        ({"data": {"id": "42", "attributes": None}}, "attributes is None"),
        ({"data": {"id": "42", "attributes": "a-string"}}, "attributes wrong type"),
        ({"data": "not-a-dict"}, "data wrong type"),
    ],
    ids=lambda v: v if isinstance(v, str) else "payload",
)
def test_get_remote_identity_by_id_malformed_shapes_return_not_found(
    monkeypatch, payload, scenario
):
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(status_code=200, json=lambda: payload)

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    result = remote_identity.get_remote_identity_by_id(42)
    assert result["error_kind"] == "mcp_input_validation", f"scenario: {scenario}"
