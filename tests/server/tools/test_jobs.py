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
        captured_payloads = []

        def fake_post(url, headers=None, json=None, timeout=None):
            captured_payloads.append(json)
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
        payload_attrs = captured_payloads[0]["data"]["attributes"]
        assert payload_attrs["start_date"] == "2024-01-01"
        assert payload_attrs["end_date"] == "2024-01-31"

    def test_create_job_payload_dates_not_swapped(self, monkeypatch, mock_auth_headers, mock_history_api_url):
        """Verify start_date and end_date in payload match arguments (no swap)."""
        captured = []

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.append(json)
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {"data": {"attributes": {"job_id": "1"}}},
            )

        monkeypatch.setattr("src.server.tools.jobs.requests.post", fake_post)

        jobs.create_job(
            subscription_id=99,
            date_start="2024-06-01",
            date_end="2024-06-30",
            stage_ids=[1001],
        )

        attrs = captured[0]["data"]["attributes"]
        assert attrs["start_date"] == "2024-06-01"
        assert attrs["end_date"] == "2024-06-30"
        assert attrs["stage_id"] == 1001

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

    def test_get_job_by_id_returns_job_on_success(self, monkeypatch, mock_auth_headers):
        monkeypatch.setattr("src.server.tools.jobs.JOBS_API_BASE_URL", "https://jobs.api.test")

        def fake_get(url, headers=None, timeout=None):
            assert url == "https://jobs.api.test/jobs/555"
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {"data": {"id": 555, "status": "active"}},
            )

        monkeypatch.setattr("src.server.tools.jobs.requests.get", fake_get)

        result = jobs.get_job_by_id(555)

        assert result == {"id": 555, "status": "active"}

    def test_get_job_by_id_returns_none_on_request_error(self, monkeypatch, mock_auth_headers):
        monkeypatch.setattr("src.server.tools.jobs.JOBS_API_BASE_URL", "https://jobs.api.test")

        def fake_get(url, headers=None, timeout=None):
            raise requests.RequestException("not found")

        monkeypatch.setattr("src.server.tools.jobs.requests.get", fake_get)

        result = jobs.get_job_by_id(555)

        assert result is None

    def test_get_history_by_id_returns_data_on_success(self, monkeypatch, mock_auth_headers):
        monkeypatch.setattr("src.server.tools.jobs.HISTORY_API_BASE_URL", "https://history.api.test")

        def fake_get(url, headers=None, timeout=None):
            assert url == "https://history.api.test/history/42"
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {"data": {"id": 42, "status": "pending"}},
            )

        monkeypatch.setattr("src.server.tools.jobs.requests.get", fake_get)

        result = jobs.get_history_by_id(42)

        assert result == {"id": 42, "status": "pending"}

    def test_update_history_status_returns_data_on_success(self, monkeypatch, mock_auth_headers):
        monkeypatch.setattr("src.server.tools.jobs.HISTORY_API_BASE_URL", "https://history.api.test")

        def fake_patch(url, headers=None, json=None, timeout=None):
            assert url == "https://history.api.test/history/42"
            assert json["data"]["attributes"]["status"] == "cancelled"
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {"data": {"id": 42, "status": "cancelled"}},
            )

        monkeypatch.setattr("src.server.tools.jobs.requests.patch", fake_patch)

        result = jobs.update_history_status(42, "cancelled")

        assert result == {"id": 42, "status": "cancelled"}


class TestJobsUrlDefault:
    """Regression tests for the JOBS_API_BASE_URL default.

    Earlier versions of this module shipped a default that already
    included a trailing '/jobs', which the request-building code then
    duplicated (`…/jobs/jobs/{id}`). These tests intentionally exercise
    the *real* module-level default so a future reintroduction of a
    trailing segment is caught without relying on monkeypatching.
    """

    def test_default_base_url_has_no_trailing_jobs_segment(self):
        assert not jobs.JOBS_API_BASE_URL.rstrip("/").endswith("/jobs"), (
            "JOBS_API_BASE_URL must not end with '/jobs' — request paths "
            "append '/jobs' themselves; see src/server/tools/jobs.py."
        )

    def test_get_jobs_url_does_not_duplicate_jobs_segment(self, monkeypatch, mock_auth_headers):
        captured_urls = []

        def fake_get(url, headers=None, params=None, timeout=None):
            captured_urls.append(url)
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {"data": []},
            )

        monkeypatch.setattr("src.server.tools.jobs.requests.get", fake_get)

        jobs.get_jobs(subscription_id=123)

        assert captured_urls, "get_jobs did not issue a request"
        url = captured_urls[0]
        assert "/jobs/jobs" not in url, (
            f"URL contains duplicated '/jobs/jobs' segment: {url!r}"
        )
        assert url.endswith("/jobs")

    def test_get_job_by_id_url_does_not_duplicate_jobs_segment(self, monkeypatch, mock_auth_headers):
        captured_urls = []

        def fake_get(url, headers=None, timeout=None):
            captured_urls.append(url)
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {"data": {"id": 555}},
            )

        monkeypatch.setattr("src.server.tools.jobs.requests.get", fake_get)

        jobs.get_job_by_id(555)

        assert captured_urls, "get_job_by_id did not issue a request"
        url = captured_urls[0]
        assert "/jobs/jobs/" not in url, (
            f"URL contains duplicated '/jobs/jobs/' segment: {url!r}"
        )
        assert url.endswith("/jobs/555")
