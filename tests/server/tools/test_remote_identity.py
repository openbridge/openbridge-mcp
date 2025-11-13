from types import SimpleNamespace

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


def test_get_remote_identities_stops_on_failure(monkeypatch):
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, params=None, timeout=None):
        return SimpleNamespace(status_code=500, json=lambda: {})

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    identities = remote_identity.get_remote_identities()

    assert identities == []


def test_get_remote_identity_by_id_success(monkeypatch):
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, timeout=None):
        assert url.endswith("/sri/42")
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

    identity = remote_identity.get_remote_identity_by_id("42")

    assert identity == {"id": "42", "relationships": {}, "region": "na", "status": "active"}


def test_get_remote_identity_by_id_not_found(monkeypatch):
    monkeypatch.setattr(remote_identity, "get_auth_headers", lambda ctx=None: {"Authorization": "token"})

    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(status_code=404, json=lambda: {})

    monkeypatch.setattr(remote_identity.requests, "get", fake_get)

    identity = remote_identity.get_remote_identity_by_id("missing")

    assert identity == {"error": "Remote identity missing not found."}
