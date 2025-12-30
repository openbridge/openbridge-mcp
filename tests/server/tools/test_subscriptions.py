"""Tests for subscriptions tool - covering edge cases and pagination."""

from types import SimpleNamespace

import pytest

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
