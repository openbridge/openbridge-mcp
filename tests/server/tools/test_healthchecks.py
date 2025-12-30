"""Tests for healthchecks tool - specifically covering JWT error handling."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.server.tools import healthchecks


@pytest.fixture
def mock_auth_headers():
    """Mock get_auth_headers to return valid auth headers."""
    with patch.object(
        healthchecks, "get_auth_headers", return_value={"Authorization": "Bearer valid.jwt.token"}
    ) as mock:
        yield mock


class TestGetHealthchecks:
    """Tests for get_healthchecks function."""

    def test_returns_empty_list_when_no_auth_header(self, monkeypatch):
        """When no Authorization header is present, returns empty list."""
        monkeypatch.setattr(
            "src.server.tools.healthchecks.get_auth_headers",
            lambda ctx=None: {},
        )

        result = healthchecks.get_healthchecks()

        assert result == []

    def test_returns_empty_list_when_auth_header_not_bearer(self, monkeypatch):
        """When Authorization header is not Bearer format, returns empty list."""
        monkeypatch.setattr(
            "src.server.tools.healthchecks.get_auth_headers",
            lambda ctx=None: {"Authorization": "Basic abc123"},
        )

        result = healthchecks.get_healthchecks()

        assert result == []

    def test_returns_empty_list_when_jwt_decode_fails(self, monkeypatch):
        """When JWT token is malformed and decode fails, returns empty list gracefully."""
        monkeypatch.setattr(
            "src.server.tools.healthchecks.get_auth_headers",
            lambda ctx=None: {"Authorization": "Bearer not-a-valid-jwt"},
        )
        # jwt.decode will raise DecodeError for invalid tokens
        # No need to mock - the real decode will fail on "not-a-valid-jwt"

        result = healthchecks.get_healthchecks()

        assert result == []

    def test_returns_empty_list_when_account_id_missing(self, monkeypatch):
        """When JWT is valid but missing account_id, returns empty list."""
        monkeypatch.setattr(
            "src.server.tools.healthchecks.get_auth_headers",
            lambda ctx=None: {"Authorization": "Bearer valid.jwt.token"},
        )
        # Mock jwt.decode to return payload without account_id
        monkeypatch.setattr(
            "src.server.tools.healthchecks.jwt.decode",
            lambda *args, **kwargs: {"user_id": "123"},  # No account_id
        )

        result = healthchecks.get_healthchecks()

        assert result == []

    def test_returns_healthchecks_on_success(self, monkeypatch):
        """When JWT is valid with account_id, returns healthchecks data."""
        monkeypatch.setattr(
            "src.server.tools.healthchecks.get_auth_headers",
            lambda ctx=None: {"Authorization": "Bearer valid.jwt.token"},
        )
        monkeypatch.setattr(
            "src.server.tools.healthchecks.jwt.decode",
            lambda *args, **kwargs: {"account_id": "12345"},
        )

        def fake_get(url, headers=None, params=None, timeout=None):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "results": [{"id": 1, "status": "ERROR"}],
                    "links": {"next": None},
                },
            )

        monkeypatch.setattr("src.server.tools.healthchecks.requests.get", fake_get)

        result = healthchecks.get_healthchecks()

        assert len(result) == 1
        assert result[0]["status"] == "ERROR"

    def test_respects_max_pages_limit(self, monkeypatch):
        """Pagination stops at HEALTHCHECKS_MAX_PAGES limit."""
        monkeypatch.setattr(
            "src.server.tools.healthchecks.get_auth_headers",
            lambda ctx=None: {"Authorization": "Bearer valid.jwt.token"},
        )
        monkeypatch.setattr(
            "src.server.tools.healthchecks.jwt.decode",
            lambda *args, **kwargs: {"account_id": "12345"},
        )

        page_count = [0]

        def fake_get(url, headers=None, params=None, timeout=None):
            page_count[0] += 1
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "results": [{"id": page_count[0]}],
                    "links": {"next": "/next-page"},  # Always has next
                },
            )

        monkeypatch.setattr("src.server.tools.healthchecks.requests.get", fake_get)
        # Override safe_pagination_url to always return a valid URL
        monkeypatch.setattr(
            "src.server.tools.healthchecks.safe_pagination_url",
            lambda next_url, base_url: "/next-page" if next_url else None,
        )

        result = healthchecks.get_healthchecks()

        # Should stop at HEALTHCHECKS_MAX_PAGES (10)
        assert page_count[0] == healthchecks.HEALTHCHECKS_MAX_PAGES
        assert len(result) == healthchecks.HEALTHCHECKS_MAX_PAGES
