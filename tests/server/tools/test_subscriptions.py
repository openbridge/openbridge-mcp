"""Tests for subscriptions tool - covering edge cases and pagination."""

from types import SimpleNamespace

import pytest
import requests as _requests
from pydantic import ValidationError

from src.server.tools import subscriptions


@pytest.fixture
def mock_auth_headers(monkeypatch):
    """Mock get_auth_headers to return valid auth headers."""
    monkeypatch.setattr(
        "src.server.tools.subscriptions.get_auth_headers",
        lambda ctx=None: {"Authorization": "Bearer valid.jwt.token"},
    )


@pytest.fixture
def mock_subscriptions_api(monkeypatch):
    """Mock SUBSCRIPTIONS_API_BASE_URL environment variable."""
    monkeypatch.setattr(
        "src.server.tools.subscriptions.SUBSCRIPTIONS_API_BASE_URL",
        "https://subscriptions.api.test",
    )


class TestGetSubscriptions:
    """Tests for get_subscriptions function."""

    def test_returns_subscriptions_on_success(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        """When API call succeeds, returns subscription list."""
        def fake_get(url, headers=None, params=None, timeout=None):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "data": [{"id": 1}, {"id": 2}],
                    "links": {"next": None},
                },
            )

        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        result = subscriptions.get_subscriptions()

        assert len(result) == 2
        assert result[0]["id"] == 1

    def test_respects_max_pages_limit(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        """Pagination stops at SUBSCRIPTIONS_MAX_PAGES limit."""
        page_count = [0]

        def fake_get(url, headers=None, params=None, timeout=None):
            page_count[0] += 1
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "data": [{"id": page_count[0]}],
                    "links": {"next": "/next-page"},  # Always has next
                },
            )

        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)
        # Override safe_pagination_url to always return a valid URL
        monkeypatch.setattr(
            "src.server.tools.subscriptions.safe_pagination_url",
            lambda next_url, base_url: "/next-page" if next_url else None,
        )

        result = subscriptions.get_subscriptions()

        # Should stop at SUBSCRIPTIONS_MAX_PAGES (10)
        assert page_count[0] == subscriptions.SUBSCRIPTIONS_MAX_PAGES
        assert len(result) == subscriptions.SUBSCRIPTIONS_MAX_PAGES

    def test_returns_empty_list_on_api_error(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        """When API returns error status, returns empty list."""
        def fake_get(url, headers=None, params=None, timeout=None):
            return SimpleNamespace(
                status_code=500,
                text="Internal Server Error",
                json=lambda: {},
            )

        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        result = subscriptions.get_subscriptions()

        assert result == []


class TestGetStorageSubscriptions:
    """Tests for get_storage_subscriptions function."""

    def test_handles_empty_spm_response(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        """When SPM response is empty, sets storage_type to 'unknown' without error."""
        # Mock storages API response
        def fake_get(url, headers=None, params=None, timeout=None):
            if "storages" in url:
                return SimpleNamespace(
                    status_code=200,
                    raise_for_status=lambda: None,
                    json=lambda: {
                        "data": [{
                            "id": "sub-1",
                            "attributes": {"storage_group_id": "sg-1"},
                        }],
                        "included": [{
                            "id": "sg-1",
                            "attributes": {"key_name": "test-key", "name": "Test Storage"},
                        }],
                    },
                )
            elif "spm" in url:
                # Empty SPM response
                return SimpleNamespace(
                    status_code=200,
                    raise_for_status=lambda: None,
                    json=lambda: {"data": []},
                )
            return SimpleNamespace(
                status_code=404,
                raise_for_status=lambda: None,
                json=lambda: {},
            )

        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        result = subscriptions.get_storage_subscriptions()

        assert len(result) == 1
        assert result[0]["storage_type"] == "unknown"
        assert result[0]["key_name"] == "test-key"
        assert result[0]["name"] == "Test Storage"

    def test_extracts_storage_type_from_spm(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        """When SPM has product name, maps it to storage type."""
        def fake_get(url, headers=None, params=None, timeout=None):
            if "storages" in url:
                return SimpleNamespace(
                    status_code=200,
                    raise_for_status=lambda: None,
                    json=lambda: {
                        "data": [{
                            "id": "sub-1",
                            "attributes": {"storage_group_id": "sg-1"},
                        }],
                        "included": [{
                            "id": "sg-1",
                            "attributes": {"key_name": "bq-key", "name": "BQ Storage"},
                        }],
                    },
                )
            elif "spm" in url:
                return SimpleNamespace(
                    status_code=200,
                    raise_for_status=lambda: None,
                    json=lambda: {
                        "data": [{
                            "attributes": {
                                "data_key": "dataset_id",
                                "data_value": "my_dataset",
                                "product": {"name": "Google BigQuery"},
                            },
                        }],
                    },
                )
            return SimpleNamespace(
                status_code=404,
                raise_for_status=lambda: None,
                json=lambda: {},
            )

        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        result = subscriptions.get_storage_subscriptions()

        assert len(result) == 1
        assert result[0]["storage_type"] == "bigquery"
        assert result[0]["dataset_id"] == "my_dataset"

    def test_handles_missing_product_name(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        """When SPM has data but no product name, sets storage_type to 'unknown'."""
        def fake_get(url, headers=None, params=None, timeout=None):
            if "storages" in url:
                return SimpleNamespace(
                    status_code=200,
                    raise_for_status=lambda: None,
                    json=lambda: {
                        "data": [{
                            "id": "sub-1",
                            "attributes": {"storage_group_id": "sg-1"},
                        }],
                        "included": [{
                            "id": "sg-1",
                            "attributes": {"key_name": "test-key", "name": "Test Storage"},
                        }],
                    },
                )
            elif "spm" in url:
                # SPM with data but no product info
                return SimpleNamespace(
                    status_code=200,
                    raise_for_status=lambda: None,
                    json=lambda: {
                        "data": [{
                            "attributes": {
                                "data_key": "dataset_id",
                                "data_value": "my_dataset",
                                # No product field
                            },
                        }],
                    },
                )
            return SimpleNamespace(
                status_code=404,
                raise_for_status=lambda: None,
                json=lambda: {},
            )

        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        result = subscriptions.get_storage_subscriptions()

        assert len(result) == 1
        assert result[0]["storage_type"] == "unknown"

    def test_handles_missing_included_record(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        """When no included record matches storage_group_id, uses defaults."""
        def fake_get(url, headers=None, params=None, timeout=None):
            if "storages" in url:
                return SimpleNamespace(
                    status_code=200,
                    raise_for_status=lambda: None,
                    json=lambda: {
                        "data": [{
                            "id": "sub-1",
                            "attributes": {"storage_group_id": "sg-999"},
                        }],
                        "included": [{
                            "id": "sg-1",
                            "attributes": {"key_name": "test-key", "name": "Test Storage"},
                        }],
                    },
                )
            elif "spm" in url:
                return SimpleNamespace(
                    status_code=200,
                    raise_for_status=lambda: None,
                    json=lambda: {"data": []},
                )
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {})
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)
        result = subscriptions.get_storage_subscriptions()
        assert len(result) == 1
        assert result[0]["name"] == "Unknown"
        assert result[0]["key_name"] == "unknown"


class TestSubscriptionMutations:
    def test_create_subscription_returns_data_on_success(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        def fake_post(url, headers=None, json=None, timeout=None):
            assert url == "https://subscriptions.api.test/sub"
            assert json["data"]["attributes"]["name"] == "test-sub"
            return SimpleNamespace(
                status_code=201,
                json=lambda: {"data": {"id": "123", "attributes": {"name": "test-sub"}}},
            )

        monkeypatch.setattr("src.server.tools.subscriptions.requests.post", fake_post)

        result = subscriptions.create_subscription({"name": "test-sub"})

        assert result["id"] == "123"

    def test_update_subscription_returns_data_on_success(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        def fake_patch(url, headers=None, json=None, timeout=None):
            assert url == "https://subscriptions.api.test/sub/123"
            assert json["data"]["id"] == "123"
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"data": {"id": "123", "attributes": {"status": "active"}}},
            )

        monkeypatch.setattr("src.server.tools.subscriptions.requests.patch", fake_patch)

        result = subscriptions.update_subscription(123, {"status": "active"})

        assert result["id"] == "123"

    def test_cancel_subscription_calls_update(self, monkeypatch):
        def fake_update(subscription_id, attributes, ctx=None):
            assert subscription_id == 123
            assert attributes == {"status": "cancelled"}
            return {"id": "123", "attributes": {"status": "cancelled"}}

        monkeypatch.setattr(subscriptions, "update_subscription", fake_update)

        result = subscriptions.cancel_subscription(123)

        assert result["attributes"]["status"] == "cancelled"

    def test_handles_no_included_key(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        """When response has no 'included' key, uses defaults."""
        def fake_get(url, headers=None, params=None, timeout=None):
            if "storages" in url:
                return SimpleNamespace(
                    status_code=200,
                    raise_for_status=lambda: None,
                    json=lambda: {
                        "data": [{
                            "id": "sub-1",
                            "attributes": {"storage_group_id": "sg-1"},
                        }],
                    },
                )
            elif "spm" in url:
                return SimpleNamespace(
                    status_code=200,
                    raise_for_status=lambda: None,
                    json=lambda: {"data": []},
                )
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {})
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)
        result = subscriptions.get_storage_subscriptions()
        assert len(result) == 1
        assert result[0]["name"] == "Unknown"
        assert result[0]["key_name"] == "unknown"


# ---------------------------------------------------------------------------
# Phase 2c — error path coverage for subscriptions
#
# Contracts under test (docs/tool-contracts.md):
# - get_subscriptions: fail-fast on non-200 OR RequestException (returns [])
#   — intentional divergence from get_remote_identities partial-results.
# - get_subscription_by_id / create / update: RequestException and
#   non-JSON return None, never propagate.
# - get_storage_subscriptions: best-effort per storage. Storages API
#   failure returns []; individual SPM failures skip that storage with
#   storage_type='unknown' but keep others.
# ---------------------------------------------------------------------------


class TestGetSubscriptionsFailFast:
    """get_subscriptions must fail-fast — intentional choice for complete-inventory semantics."""

    def test_request_exception_returns_empty(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        def raising_get(*args, **kwargs):
            raise _requests.ConnectionError("subscriptions API down")
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", raising_get)

        assert subscriptions.get_subscriptions() == []

    def test_partial_results_are_discarded_on_mid_pagination_failure(
        self, monkeypatch, mock_auth_headers, mock_subscriptions_api
    ):
        """Distinguishing mark from get_remote_identities: rows from page 1
        are NOT returned if page 2 fails. Callers expect a complete list."""
        responses = [
            SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "data": [{"id": 1}, {"id": 2}],
                    "links": {"next": "https://subscriptions.api.test/sub?page=2"},
                },
            ),
            SimpleNamespace(status_code=500, text="server error", json=lambda: {}),
        ]

        def fake_get(url, headers=None, params=None, timeout=None):
            return responses.pop(0)

        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        assert subscriptions.get_subscriptions() == []


class TestGetSubscriptionByIdErrorPaths:
    def test_non_200_returns_none(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        def fake_get(url, headers=None, timeout=None):
            return SimpleNamespace(status_code=404, text="not found")
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        result = subscriptions.get_subscription_by_id(999)
        assert result["error_kind"] == "mcp_input_validation"

    def test_request_exception_returns_none(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        def raising_get(*args, **kwargs):
            raise _requests.Timeout("slow")
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", raising_get)

        result = subscriptions.get_subscription_by_id(999)
        assert result["error_kind"] == "sp_api_client"

    def test_non_json_returns_none(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        def fake_get(url, headers=None, timeout=None):
            return SimpleNamespace(
                status_code=200,
                text="<html>",
                json=lambda: (_ for _ in ()).throw(ValueError("not json")),
            )
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        result = subscriptions.get_subscription_by_id(999)
        assert result["error_kind"] == "mcp_input_validation"

    def test_missing_data_key_returns_none(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        def fake_get(url, headers=None, timeout=None):
            return SimpleNamespace(status_code=200, text="{}", json=lambda: {})
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        result = subscriptions.get_subscription_by_id(999)
        assert result["error_kind"] == "mcp_input_validation"


class TestCreateUpdateSubscriptionErrorPaths:
    def test_create_request_exception_returns_none(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        def raising_post(*args, **kwargs):
            raise _requests.ConnectionError("down")
        monkeypatch.setattr("src.server.tools.subscriptions.requests.post", raising_post)

        assert subscriptions.create_subscription({"product": 1}) is None

    def test_create_non_json_returns_none(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        def fake_post(*args, **kwargs):
            return SimpleNamespace(
                status_code=201,
                text="<html>",
                json=lambda: (_ for _ in ()).throw(ValueError("not json")),
            )
        monkeypatch.setattr("src.server.tools.subscriptions.requests.post", fake_post)

        assert subscriptions.create_subscription({"product": 1}) is None

    def test_update_request_exception_returns_none(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        def raising_patch(*args, **kwargs):
            raise _requests.ConnectionError("down")
        monkeypatch.setattr("src.server.tools.subscriptions.requests.patch", raising_patch)

        assert subscriptions.update_subscription(1, {"status": "cancelled"}) is None

    def test_update_non_json_returns_none(self, monkeypatch, mock_auth_headers, mock_subscriptions_api):
        def fake_patch(*args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                text="<html>",
                json=lambda: (_ for _ in ()).throw(ValueError("not json")),
            )
        monkeypatch.setattr("src.server.tools.subscriptions.requests.patch", fake_patch)

        assert subscriptions.update_subscription(1, {}) is None


def test_update_subscription_rejects_string_id():
    with pytest.raises(ValidationError):
        subscriptions.update_subscription("1", {"status": "active"})


class TestGetStorageSubscriptionsBestEffort:
    """REGRESSION suite for the alignment PR: storage subscription failures
    must be soft (partial results), not hard (entire call fails)."""

    def test_storages_api_network_error_returns_empty(
        self, monkeypatch, mock_auth_headers, mock_subscriptions_api
    ):
        """Complete outage of the storages API: no work to attempt, return []."""
        def raising_get(*args, **kwargs):
            raise _requests.ConnectionError("storages API down")
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", raising_get)

        assert subscriptions.get_storage_subscriptions() == []

    def test_storages_api_non_200_returns_empty(
        self, monkeypatch, mock_auth_headers, mock_subscriptions_api
    ):
        def fake_get(url, headers=None, params=None, timeout=None):
            return SimpleNamespace(status_code=503, text="unavailable")
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        assert subscriptions.get_storage_subscriptions() == []

    def test_storages_non_json_returns_empty(
        self, monkeypatch, mock_auth_headers, mock_subscriptions_api
    ):
        def fake_get(url, headers=None, params=None, timeout=None):
            return SimpleNamespace(
                status_code=200,
                text="<html>",
                json=lambda: (_ for _ in ()).throw(ValueError("not json")),
            )
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        assert subscriptions.get_storage_subscriptions() == []

    def test_storages_missing_data_returns_empty(
        self, monkeypatch, mock_auth_headers, mock_subscriptions_api
    ):
        def fake_get(url, headers=None, params=None, timeout=None):
            return SimpleNamespace(status_code=200, text="{}", json=lambda: {"not_data": []})
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        assert subscriptions.get_storage_subscriptions() == []

    def test_spm_partial_failure_keeps_other_storages(
        self, monkeypatch, mock_auth_headers, mock_subscriptions_api
    ):
        """Two storages, one SPM call fails with 500, the other succeeds.
        Both storages must appear in results; the failed one carries
        storage_type='unknown' and no SPM keys."""
        storages_body = {
            "data": [
                {"id": "sub-1", "attributes": {"storage_group_id": "sg-1"}},
                {"id": "sub-2", "attributes": {"storage_group_id": "sg-2"}},
            ],
        }
        spm_good = {
            "data": [
                {
                    "attributes": {
                        "data_key": "dataset_id",
                        "data_value": "ds-2",
                        "product": {"name": "Google BigQuery"},
                    }
                }
            ]
        }

        def fake_get(url, headers=None, params=None, timeout=None):
            if "storages" in url:
                return SimpleNamespace(status_code=200, text="{}", json=lambda: storages_body)
            if "subscription=sub-1" in url:
                return SimpleNamespace(status_code=500, text="server error")
            if "subscription=sub-2" in url:
                return SimpleNamespace(status_code=200, text="{}", json=lambda: spm_good)
            return SimpleNamespace(status_code=404, text="unexpected")
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        result = subscriptions.get_storage_subscriptions()

        assert len(result) == 2
        by_sub = {r["subscription_id"]: r for r in result}

        # Failed storage — included with unknown type, no SPM keys leaked in.
        assert by_sub["sub-1"]["storage_type"] == "unknown"
        assert "dataset_id" not in by_sub["sub-1"]

        # Good storage — full SPM decoration.
        assert by_sub["sub-2"]["storage_type"] == "bigquery"
        assert by_sub["sub-2"]["dataset_id"] == "ds-2"

    def test_spm_network_error_keeps_other_storages(
        self, monkeypatch, mock_auth_headers, mock_subscriptions_api
    ):
        """Same as above but the failing SPM call raises RequestException
        instead of returning non-200."""
        storages_body = {
            "data": [
                {"id": "sub-1", "attributes": {"storage_group_id": "sg-1"}},
                {"id": "sub-2", "attributes": {"storage_group_id": "sg-2"}},
            ],
        }
        spm_good = {"data": []}

        def fake_get(url, headers=None, params=None, timeout=None):
            if "storages" in url:
                return SimpleNamespace(status_code=200, text="{}", json=lambda: storages_body)
            if "subscription=sub-1" in url:
                raise _requests.Timeout("spm slow")
            return SimpleNamespace(status_code=200, text="{}", json=lambda: spm_good)
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        result = subscriptions.get_storage_subscriptions()
        assert len(result) == 2
        assert {r["subscription_id"] for r in result} == {"sub-1", "sub-2"}

    @pytest.mark.parametrize(
        "spm_body,scenario",
        [
            ({"data": "not-a-list"}, "data wrong type"),
            ({"data": [{"attributes": None}]}, "attributes is None"),
            ({"data": [{"not_attributes": {}}]}, "item missing attributes"),
            ({"data": [{"attributes": {"data_key": None}}]}, "data_key None"),
        ],
        ids=lambda v: v if isinstance(v, str) else "spm_body",
    )
    def test_spm_malformed_shapes_included_with_unknown_type(
        self, monkeypatch, mock_auth_headers, mock_subscriptions_api, spm_body, scenario
    ):
        """Malformed SPM shapes for a single storage must not crash; the
        storage is returned with storage_type='unknown'."""
        storages_body = {
            "data": [{"id": "sub-1", "attributes": {"storage_group_id": "sg-1"}}],
        }

        def fake_get(url, headers=None, params=None, timeout=None):
            if "storages" in url:
                return SimpleNamespace(status_code=200, text="{}", json=lambda: storages_body)
            return SimpleNamespace(status_code=200, text="{}", json=lambda: spm_body)
        monkeypatch.setattr("src.server.tools.subscriptions.requests.get", fake_get)

        result = subscriptions.get_storage_subscriptions()
        assert len(result) == 1, f"scenario: {scenario}"
        assert result[0]["storage_type"] == "unknown", f"scenario: {scenario}"
