"""Tests for jobs tool - specifically covering exception handling."""

from types import SimpleNamespace

import pytest
import requests

from src.server.tools import jobs


@pytest.fixture
def mock_auth_headers(monkeypatch):
    """Mock get_auth_headers to return valid auth headers."""
    monkeypatch.setattr(
        "src.server.tools.jobs.get_auth_headers",
        lambda ctx=None: {"Authorization": "Bearer valid.jwt.token"},
    )


@pytest.fixture
def mock_history_api_url(monkeypatch):
    """Mock HISTORY_API_BASE_URL environment variable."""
    monkeypatch.setenv("HISTORY_API_BASE_URL", "https://history.api.test")


class TestCreateJob:
    """Tests for create_job function."""

    def test_returns_job_data_on_success(self, monkeypatch, mock_auth_headers, mock_history_api_url):
        """When API call succeeds, returns job data."""
        def fake_post(url, headers=None, json=None, timeout=None):
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {
                    "data": {
                        "attributes": {
                            "job_id": "12345",
                            "status": "pending",
                        }
                    }
                },
            )

        monkeypatch.setattr("src.server.tools.jobs.requests.post", fake_post)

        result = jobs.create_job(
            subscription_id=123,
            date_start="2024-01-01",
            date_end="2024-01-31",
            stage_ids=[1],
        )

        assert len(result) == 1
        assert result[0]["job_id"] == "12345"
        assert result[0]["status"] == "pending"

    def test_handles_request_exception_with_no_response(self, monkeypatch, mock_auth_headers, mock_history_api_url):
        """When requests raises exception before response, uses exception message."""
        def fake_post(url, headers=None, json=None, timeout=None):
            raise requests.RequestException("Connection refused")

        monkeypatch.setattr("src.server.tools.jobs.requests.post", fake_post)

        result = jobs.create_job(
            subscription_id=123,
            date_start="2024-01-01",
            date_end="2024-01-31",
            stage_ids=[1],
        )

        assert len(result) == 1
        assert "errors" in result[0]
        assert "Connection refused" in result[0]["errors"]

    def test_handles_request_exception_with_response_text(self, monkeypatch, mock_auth_headers, mock_history_api_url):
        """When response exists but raises on status check, uses response text."""
        def fake_post(url, headers=None, json=None, timeout=None):
            response = SimpleNamespace(
                status_code=400,
                text='{"error": "Bad request: invalid date format"}',
            )
            response.raise_for_status = lambda: (_ for _ in ()).throw(
                requests.HTTPError("400 Client Error")
            )
            return response

        monkeypatch.setattr("src.server.tools.jobs.requests.post", fake_post)

        result = jobs.create_job(
            subscription_id=123,
            date_start="2024-01-01",
            date_end="2024-01-31",
            stage_ids=[1],
        )

        assert len(result) == 1
        assert "errors" in result[0]
        assert "Bad request" in result[0]["errors"]

    def test_creates_jobs_for_multiple_stage_ids(self, monkeypatch, mock_auth_headers, mock_history_api_url):
        """When multiple stage_ids provided, creates job for each."""
        call_count = [0]

        def fake_post(url, headers=None, json=None, timeout=None):
            call_count[0] += 1
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {
                    "data": {
                        "attributes": {
                            "job_id": f"job-{call_count[0]}",
                            "stage_id": json["data"]["attributes"]["stage_id"],
                        }
                    }
                },
            )

        monkeypatch.setattr("src.server.tools.jobs.requests.post", fake_post)

        result = jobs.create_job(
            subscription_id=123,
            date_start="2024-01-01",
            date_end="2024-01-31",
            stage_ids=[1, 2, 3],
        )

        assert len(result) == 3
        assert call_count[0] == 3


class TestGetJobs:
    """Tests for get_jobs function."""

    def test_returns_jobs_on_success(self, monkeypatch, mock_auth_headers):
        """When API call succeeds, returns job list."""
        monkeypatch.setattr("src.server.tools.jobs.JOBS_API_BASE_URL", "https://jobs.api.test")

        def fake_get(url, headers=None, params=None, timeout=None):
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {
                    "data": [
                        {"id": 1, "status": "active"},
                        {"id": 2, "status": "active"},
                    ]
                },
            )

        monkeypatch.setattr("src.server.tools.jobs.requests.get", fake_get)

        result = jobs.get_jobs(subscription_id=123)

        assert len(result) == 2
        assert result[0]["id"] == 1

    def test_returns_empty_list_on_request_error(self, monkeypatch, mock_auth_headers):
        """When requests raises exception, returns empty list."""
        monkeypatch.setattr("src.server.tools.jobs.JOBS_API_BASE_URL", "https://jobs.api.test")

        def fake_get(url, headers=None, params=None, timeout=None):
            raise requests.RequestException("Connection failed")

        monkeypatch.setattr("src.server.tools.jobs.requests.get", fake_get)

        result = jobs.get_jobs(subscription_id=123)

        assert result == []
