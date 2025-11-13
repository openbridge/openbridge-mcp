from types import SimpleNamespace

from src.server.tools import base
from src.auth.simple import AuthenticationError


def test_get_auth_headers_without_token(monkeypatch):
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace()

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    headers = base.get_auth_headers()

    assert headers == {}
    assert calls == []


def test_get_auth_headers_converts_refresh_token(monkeypatch):
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "abc:def")

    def fake_post(url, json, headers, timeout):
        assert url.endswith("/auth/api/ref")
        assert json["data"]["attributes"]["refresh_token"] == "abc:def"
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": "jwt-token"}}},
        )

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)
    monkeypatch.setattr("src.auth.simple.time.time", lambda: 1000)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 2000})

    headers = base.get_auth_headers()

    assert headers == {"Authorization": "Bearer jwt-token"}

def test_get_auth_headers_raises_on_conversion_failure(monkeypatch):
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "abc:def")

    def fake_post(*args, **kwargs):
        raise RuntimeError("network failure")

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    try:
        base.get_auth_headers()
        assert False, "Expected AuthenticationError"
    except AuthenticationError as exc:
        assert "Failed to convert OPENBRIDGE_REFRESH_TOKEN to JWT" in str(exc)
