"""Tests for healthchecks tool - specifically covering JWT error handling."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

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
        """When no Authorization header is present, returns auth envelope."""
        monkeypatch.setattr(
            "src.server.tools.healthchecks.get_auth_headers",
            lambda ctx=None: {},
        )

        result = healthchecks.get_healthchecks()

        assert result["error_kind"] == "auth_error"
        assert result["_envelope_version"] == 1

    def test_returns_empty_list_when_auth_header_not_bearer(self, monkeypatch):
        """When Authorization header is not Bearer format, returns auth envelope."""
        monkeypatch.setattr(
            "src.server.tools.healthchecks.get_auth_headers",
            lambda ctx=None: {"Authorization": "Basic abc123"},
        )

        result = healthchecks.get_healthchecks()

        assert result["error_kind"] == "auth_error"

    def test_returns_empty_list_when_jwt_decode_fails(self, monkeypatch):
        """When JWT decode fails, returns auth envelope."""
        monkeypatch.setattr(
            "src.server.tools.healthchecks.get_auth_headers",
            lambda ctx=None: {"Authorization": "Bearer not-a-valid-jwt"},
        )
        # jwt.decode will raise DecodeError for invalid tokens
        # No need to mock - the real decode will fail on "not-a-valid-jwt"

        result = healthchecks.get_healthchecks()

        assert result["error_kind"] == "auth_error"

    def test_returns_empty_list_when_account_id_missing(self, monkeypatch):
        """When JWT is valid but missing account_id, returns auth envelope."""
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

        assert result["error_kind"] == "auth_error"

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

    def test_filter_date_and_last_days_are_mutually_exclusive(self):
        result = healthchecks.get_healthchecks(filter_date="2024-01-01", last_days=7)
        assert result["error_kind"] == "mcp_input_validation"
        assert "mutually exclusive" in result["summary"]

    def test_last_days_sets_modified_at_gt(self, monkeypatch):
        monkeypatch.setattr(
            "src.server.tools.healthchecks.get_auth_headers",
            lambda ctx=None: {"Authorization": "Bearer valid.jwt.token"},
        )
        monkeypatch.setattr(
            "src.server.tools.healthchecks.jwt.decode",
            lambda *args, **kwargs: {"account_id": "12345"},
        )
        captured = {}

        def fake_get(url, headers=None, params=None, timeout=None):
            captured["params"] = params
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"results": [], "links": {"next": None}},
            )

        monkeypatch.setattr("src.server.tools.healthchecks.requests.get", fake_get)
        healthchecks.get_healthchecks(last_days=7)
        assert "modified_at__gt" in captured["params"]
        assert captured["params"]["page"] == 1

    def test_explicit_page_disables_auto_pagination(self, monkeypatch):
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
                json=lambda: {"results": [{"id": 1}], "links": {"next": "/next"}},
            )

        monkeypatch.setattr("src.server.tools.healthchecks.requests.get", fake_get)
        monkeypatch.setattr(
            "src.server.tools.healthchecks.safe_pagination_url",
            lambda next_url, base_url: "/next" if next_url else None,
        )

        result = healthchecks.get_healthchecks(page=2)
        assert page_count[0] == 1
        assert len(result) == 1

    def test_subscription_id_rejects_string(self):
        with pytest.raises(ValidationError):
            healthchecks.get_healthchecks(subscription_id="1")
