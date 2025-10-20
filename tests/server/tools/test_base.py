from types import SimpleNamespace

from src.server.tools import base


def test_get_auth_headers_without_token(monkeypatch):
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace()

    monkeypatch.setattr(base.requests, "post", fake_post)

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

    monkeypatch.setattr(base.requests, "post", fake_post)

    headers = base.get_auth_headers()

    assert headers == {"Authorization": "Bearer jwt-token"}


def test_get_auth_headers_falls_back_to_refresh_token(monkeypatch):
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "abc:def")

    def fake_post(*args, **kwargs):
        raise RuntimeError("network failure")

    monkeypatch.setattr(base.requests, "post", fake_post)

    headers = base.get_auth_headers()

    assert headers == {"Authorization": "Bearer abc:def"}
